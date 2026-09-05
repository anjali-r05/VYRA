import logging
import os
import re
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config, DEFAULT_HIGH_PRIORITY_CATEGORIES, DEFAULT_LOW_PRIORITY_CATEGORIES, ALL_CATEGORIES
from services.database import db
from agent.analyzer_v3 import analyze_message_v3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vyra")

# Well-known identifier for the single V1 default user. V1 has no auth system,
# so every request operates as this one seeded user. Giving it a fixed unique
# email (rather than relying on "first row in the table") lets concurrent
# Gunicorn workers detect a duplicate-seed attempt via the UNIQUE constraint
# instead of racing on an unconstrained "is the table empty" check.
DEFAULT_USER_EMAIL = "default@vyra.local"


def get_or_create_default_user():
    """
    Idempotent, concurrency-safe get-or-create.

    Under multiple Gunicorn workers, several processes can reach this
    function at the same time on first boot, all see "no user exists yet",
    and all attempt to INSERT. Only one INSERT can win because of the
    UNIQUE constraint on User.email. The others must not crash — they
    catch that specific IntegrityError, roll back their own failed insert,
    and re-query to pick up the row the winning worker committed.

    This is deliberately NOT a blanket try/except: only IntegrityError is
    caught here, and only to recover into a successful re-query. Any other
    exception still propagates.
    """
    from models import User

    user = User.query.filter_by(email=DEFAULT_USER_EMAIL).first()
    if user:
        return user

    user = User(name="Default User", email=DEFAULT_USER_EMAIL)
    db.session.add(user)
    try:
        db.session.commit()
        logger.info("Created default user")
    except IntegrityError:
        # Another worker won the race and committed the default user first.
        # Roll back this worker's failed insert, then re-fetch the row that
        # actually exists. This is the recovery path, not a silent ignore.
        db.session.rollback()
        user = User.query.filter_by(email=DEFAULT_USER_EMAIL).first()
        if user is None:
            # Should be unreachable: a UNIQUE violation means a committed
            # row exists. If it's still missing, something else is wrong
            # and we must not paper over it.
            raise
        logger.info("Default user already existed (created by another worker); reused it")

    return user



def get_current_user():
    """Return the signed-in user, or the legacy default workspace for existing V1/V2 installs."""
    from models import User
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, user_id)
    if session.get("logged_out"):
        return None
    return get_or_create_default_user()


def user_owns_message(user_id, message):
    return bool(message and message.user_id == user_id)


def migrate_legacy_messages_to_default_user(default_user):
    """Attach pre-auth V1/V2 messages to the legacy workspace exactly once."""
    from models import Message
    legacy = Message.query.filter(Message.user_id.is_(None)).all()
    if not legacy:
        return
    for message in legacy:
        message.user_id = default_user.id
    db.session.commit()
    logger.info("Attached %d legacy messages to the default VYRA workspace", len(legacy))


def get_user_preferences(user_id):
    from models import Preference
    prefs = Preference.query.filter_by(user_id=user_id).all()
    pref_map = {p.category: p.priority_level for p in prefs}
    if not pref_map:
        for cat in DEFAULT_HIGH_PRIORITY_CATEGORIES:
            pref_map[cat] = "high"
        for cat in DEFAULT_LOW_PRIORITY_CATEGORIES:
            pref_map[cat] = "low"
    return pref_map


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Read DATABASE_URL at app-creation time, not at config-module import
    # time. This is important for pytest, where each test creates an isolated
    # temporary SQLite database by setting DATABASE_URL immediately before
    # calling create_app().
    database_url = os.environ.get("DATABASE_URL", Config.SQLALCHEMY_DATABASE_URI)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url

    # SQLite's default connection pool can retain an open file handle after
    # a test client exits. Windows refuses to unlink a still-open SQLite
    # database, producing WinError 32 during pytest teardown. NullPool closes
    # each SQLite connection when its SQLAlchemy session releases it, while
    # PostgreSQL keeps its normal pooling behavior.
    if database_url.startswith("sqlite:"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"poolclass": NullPool}

    db.init_app(app)

    with app.app_context():
        from models import User, Message, Decision, Feedback, Preference, Thread, Task, Action  # noqa: F401
        from sqlalchemy import inspect

        try:
            db.create_all()
        except OperationalError as e:
            # Concurrent Gunicorn workers can both reach create_all() on
            # first boot. SQLAlchemy's create_all() checks "does this table
            # exist" then issues CREATE TABLE, and that check-then-act is
            # not atomic across separate processes, so two workers can both
            # decide a table is missing and both try to create it. SQLite
            # (and most DBs) reports the loser as "table already exists".
            # That specific outcome means the schema now exists — which is
            # exactly the state create_all() was trying to reach — so it is
            # safe to proceed, but we verify it explicitly rather than
            # assuming the error was benign.
            if "already exists" not in str(e).lower():
                raise
            logger.info("Schema already created by another worker during startup race; continuing")

        # Verify every required table actually exists before proceeding,
        # regardless of which branch above ran. If this fails, initialization
        # genuinely did not succeed and we must not continue silently.
        inspector = inspect(db.engine)
        existing_tables = set(inspector.get_table_names())
        required_tables = {"users", "messages", "decisions", "feedback", "preferences", "threads", "tasks", "actions", "user_credentials"}
        missing = required_tables - existing_tables
        if missing:
            raise RuntimeError(f"Database initialization incomplete — missing tables: {missing}")

        default_user = get_or_create_default_user()
        migrate_legacy_messages_to_default_user(default_user)
        from models import UserCredential
        if not UserCredential.query.filter_by(user_id=default_user.id).first():
            db.session.add(UserCredential(user_id=default_user.id, password_hash=generate_password_hash("vyra-demo")))
            db.session.commit()

        # On production PostgreSQL, restore the curated demo workspace from
        # the bundled SQLite source exactly once. New users remain separate.
        from services.demo_seed import seed_demo_workspace
        seed_demo_workspace(db, database_url)

    @app.context_processor
    def inject_current_user():
        return {"current_user": get_current_user()}

    @app.before_request
    def establish_workspace():
        if request.endpoint in {"login", "signup", "logout", "static"}:
            return
        # Existing tests and legacy V1/V2 links keep working in the default workspace.
        # Once a user signs in, every data query below is scoped to session user_id.
        user = get_current_user()
        if user:
            session["user_id"] = user.id

    # ---------------- AUTH ROUTES ----------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            from models import User, UserCredential
            user = User.query.filter(db.func.lower(User.email) == email).first() if email else None
            credential = UserCredential.query.filter_by(user_id=user.id).first() if user else None
            if not user or not credential or not check_password_hash(credential.password_hash, password):
                return render_template("login.html", error="Invalid email or password.", email=email)
            session.clear()
            session["user_id"] = user.id
            session["logged_out"] = False
            return redirect(url_for("dashboard"))
        return render_template("login.html")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            if len(name) < 2:
                return render_template("signup.html", error="Please enter your name.", name=name, email=email)
            if "@" not in email or "." not in email.split("@")[-1]:
                return render_template("signup.html", error="Please enter a valid email.", name=name, email=email)
            if len(password) < 8:
                return render_template("signup.html", error="Password must be at least 8 characters.", name=name, email=email)
            from models import User, UserCredential
            if User.query.filter(db.func.lower(User.email) == email).first():
                return render_template("signup.html", error="An account with this email already exists.", name=name, email=email)
            user = User(name=name, email=email)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserCredential(user_id=user.id, password_hash=generate_password_hash(password)))
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            session["logged_out"] = False
            return redirect(url_for("dashboard"))
        return render_template("signup.html")

    @app.route("/logout")
    def logout():
        session.clear()
        session["logged_out"] = True
        return redirect(url_for("login"))

    # ---------------- VOICE AGENT ----------------

    @app.route("/voice")
    def voice_agent():
        return render_template("voice_agent.html", active="voice_agent")

    @app.route("/api/voice/query", methods=["POST"])
    def api_voice_query():
        from agent.voice_agent import query_voice
        user = get_current_user()
        if not user:
            return jsonify({"ok": False, "message": "Please sign in to use VYRA Voice Agent."}), 401
        payload = request.get_json(silent=True) or {}
        return jsonify(query_voice(user.id, payload.get("text", "")))

    @app.route("/api/voice/confirm", methods=["POST"])
    def api_voice_confirm():
        from agent.voice_agent import confirm_voice_action
        user = get_current_user()
        if not user:
            return jsonify({"ok": False, "message": "Please sign in to continue."}), 401
        payload = request.get_json(silent=True) or {}
        result = confirm_voice_action(user.id, payload.get("action_id"), payload.get("operation"))
        return jsonify(result), (200 if result.get("ok") else 400)

    # ---------------- PAGE ROUTES ----------------

    @app.route("/")
    def index():
        # Public product landing page. Authenticated users can enter their
        # private workspace from here; existing demo data remains available
        # after signing into the default workspace.
        return render_template("landing.html")

    @app.route("/dashboard")
    def dashboard():
        from models import Message, Decision, Feedback
        from agent.proactive_brief import generate_proactive_brief

        user = get_current_user()
        total_messages = Message.query.filter_by(user_id=user.id).count()
        feedback_total = db.session.query(Feedback).join(Message, Feedback.message_id == Message.id).filter(Message.user_id == user.id).count()
        notify_count = db.session.query(Decision).join(Message, Decision.message_id == Message.id).filter(Message.user_id == user.id, Decision.decision == "notify").count()
        digest_count = db.session.query(Decision).join(Message, Decision.message_id == Message.id).filter(Message.user_id == user.id, Decision.decision == "digest").count()
        mute_count = db.session.query(Decision).join(Message, Decision.message_id == Message.id).filter(Message.user_id == user.id, Decision.decision == "mute").count()

        avg_score_row = db.session.query(db.func.avg(Decision.attention_score)).join(Message, Decision.message_id == Message.id).filter(Message.user_id == user.id).scalar()
        avg_score = round(avg_score_row, 1) if avg_score_row else 0

        recent = (
            db.session.query(Message, Decision)
            .join(Decision, Decision.message_id == Message.id)
            .filter(Message.user_id == user.id)
            .order_by(Message.created_at.desc())
            .limit(8)
            .all()
        )

        important = (
            db.session.query(Message, Decision)
            .join(Decision, Decision.message_id == Message.id)
            .filter(Message.user_id == user.id, Decision.decision == "notify")
            .order_by(Decision.attention_score.desc())
            .limit(5)
            .all()
        )

        try:
            brief = generate_proactive_brief(user_id=user.id)
        except Exception:
            logger.exception("Proactive brief generation failed; showing dashboard without it")
            brief = {"is_empty": True, "critical_count": 0, "important_count": 0, "upcoming_count": 0,
                      "safety_count": 0, "critical_items": [], "important_items": [], "upcoming_items": [], "safety_items": [],
                      "next_move": {"label": "Brief unavailable", "detail": "Refresh to recalculate.", "type": "clear"}}

        return render_template(
            "dashboard.html",
            active="dashboard",
            total_messages=total_messages,
            notify_count=notify_count,
            digest_count=digest_count,
            mute_count=mute_count,
            avg_score=avg_score,
            recent=recent,
            important=important,
            feedback_total=feedback_total,
            brief=brief,
        )

    @app.route("/analyze")
    def analyze_page():
        return render_template("analyze.html", active="analyze")

    @app.route("/inbox")
    def inbox():
        from models import Message, Decision, Task, Action, Thread
        filter_type = request.args.get("filter", "all")

        query = db.session.query(Message, Decision).join(Decision, Decision.message_id == Message.id).filter(Message.user_id == get_current_user().id)
        if filter_type in ("notify", "digest", "mute"):
            query = query.filter(Decision.decision == filter_type)

        results = query.order_by(Message.created_at.desc()).limit(100).all()

        # V3 indicators: small, additive, read-only lookups for the messages
        # actually being displayed — same bounded-query pattern already used
        # by agent/proactive_brief.py. Never invents data: a message only
        # gets an indicator if a real Task/Action/Thread row references it.
        message_ids = [m.id for m, _ in results]
        v3_indicators = {}
        if message_ids:
            pending_task_ids = {
                t.message_id for t in Task.query.filter(
                    Task.message_id.in_(message_ids), Task.status == "pending"
                ).all()
            }
            pending_action_ids = {
                a.message_id for a in Action.query.filter(
                    Action.message_id.in_(message_ids),
                    Action.status.in_(["suggested", "approved", "edited"]),
                ).all()
            }
            thread_titles_by_message = {}
            for t in Thread.query.order_by(Thread.updated_at.desc()).limit(100).all():
                for mid in t.message_ids:
                    if mid in message_ids:
                        thread_titles_by_message[mid] = t.title

            for mid in message_ids:
                v3_indicators[mid] = {
                    "has_task": mid in pending_task_ids,
                    "has_action": mid in pending_action_ids,
                    "thread_title": thread_titles_by_message.get(mid),
                }

        return render_template(
            "inbox.html", active="inbox", results=results, filter_type=filter_type,
            v3_indicators=v3_indicators,
        )

    @app.route("/digest")
    def digest():
        from models import Message, Decision
        base_query = db.session.query(Message, Decision).join(Decision, Decision.message_id == Message.id).filter(Message.user_id == get_current_user().id)

        needs_attention = base_query.filter(Decision.decision == "notify").order_by(
            Decision.attention_score.desc()).limit(20).all()
        worth_knowing = base_query.filter(Decision.decision == "digest").order_by(
            Decision.attention_score.desc()).limit(20).all()
        filtered = base_query.filter(Decision.decision == "mute").order_by(
            Message.created_at.desc()).limit(20).all()

        return render_template(
            "digest.html",
            active="digest",
            needs_attention=needs_attention,
            worth_knowing=worth_knowing,
            filtered=filtered,
        )

    @app.route("/attention-dna")
    def attention_dna():
        from agent.personalization import get_all_category_profiles, compute_personalization_adjustment, PERSONALIZATION_MAX_ADJUSTMENT

        profiles = get_all_category_profiles(user_id=get_current_user().id)
        total_feedback_count = sum(stats.total_feedback for stats in profiles.values())

        # Only build a learned-profile entry for categories with real feedback —
        # never fabricate a "learned" row for a category with zero data.
        learned_profile = []
        for category, stats in profiles.items():
            if stats.total_feedback == 0:
                continue
            result = compute_personalization_adjustment(category, user_id=get_current_user().id)
            learned_profile.append({
                "category": category,
                "stats": stats,
                "adjustment": result.adjustment,
                "reasons": result.reasons,
                "bar_percent": round(min(100, abs(result.adjustment) / PERSONALIZATION_MAX_ADJUSTMENT * 100)),
            })
        learned_profile.sort(key=lambda row: abs(row["adjustment"]), reverse=True)

        try:
            from agent.attention_analytics import compute_attention_analytics
            v3_analytics = compute_attention_analytics(user_id=get_current_user().id)
        except Exception:
            logger.exception("V3 attention analytics failed on Attention DNA page; showing page without it")
            v3_analytics = None

        from models import Feedback, Message
        user = get_current_user()
        feedback_correct = db.session.query(Feedback).join(Message, Message.id == Feedback.message_id).filter(Message.user_id == user.id, Feedback.feedback == "correct").count()
        feedback_wrong = db.session.query(Feedback).join(Message, Message.id == Feedback.message_id).filter(Message.user_id == user.id, Feedback.feedback == "wrong").count()
        return render_template(
            "attention_dna.html",
            active="attention_dna",
            learned_profile=learned_profile,
            total_feedback_count=total_feedback_count,
            feedback_correct=feedback_correct,
            feedback_wrong=feedback_wrong,
            v3=v3_analytics,
        )

    @app.route("/analytics")
    def analytics():
        from models import Decision, Message, Feedback
        from agent.attention_analytics import compute_attention_analytics

        user = get_current_user()
        base_decisions = db.session.query(Decision).join(Message, Message.id == Decision.message_id).filter(Message.user_id == user.id)
        total = base_decisions.count()
        category_counts = (
            db.session.query(Message.category, db.func.count(Message.id))
            .filter(Message.user_id == user.id)
            .group_by(Message.category)
            .all()
        )
        decision_counts = (
            db.session.query(Decision.decision, db.func.count(Decision.id))
            .join(Message, Message.id == Decision.message_id)
            .filter(Message.user_id == user.id)
            .group_by(Decision.decision)
            .all()
        )
        avg_score_row = base_decisions.with_entities(db.func.avg(Decision.attention_score)).scalar()
        avg_score = round(avg_score_row, 1) if avg_score_row else 0

        correct = db.session.query(Feedback).join(Message, Message.id == Feedback.message_id).filter(Message.user_id == user.id, Feedback.feedback == "correct").count()
        wrong = db.session.query(Feedback).join(Message, Message.id == Feedback.message_id).filter(Message.user_id == user.id, Feedback.feedback == "wrong").count()

        try:
            v3_analytics = compute_attention_analytics(user_id=get_current_user().id)
        except Exception:
            logger.exception("V3 attention analytics failed; showing page without it")
            v3_analytics = None

        # Recent score series for the live trend chart; all rows belong to the current user.
        recent_scores = (db.session.query(Message.created_at, Decision.attention_score)
                         .join(Decision, Decision.message_id == Message.id)
                         .filter(Message.user_id == user.id)
                         .order_by(Message.created_at.asc()).limit(30).all())
        return render_template(
            "analytics.html",
            active="analytics",
            total=total,
            category_counts=category_counts,
            decision_counts=decision_counts,
            avg_score=avg_score,
            correct=correct,
            wrong=wrong,
            v3=v3_analytics,
            recent_scores=[{"label": dt.strftime("%b %d"), "score": round(float(score), 1)} for dt, score in recent_scores],
        )


    # ========================================================
    # SMART THREADS INTELLIGENCE HELPERS
    # ========================================================
    def _thread_words(text):
        return set(re.findall(r"[a-zA-Z]{4,}", (text or "").lower()))

    def _thread_change_summary(previous_text, latest_text):
        """Explain observable lexical change between two real messages."""
        prev = _thread_words(previous_text)
        latest = _thread_words(latest_text)
        added = sorted(latest - prev, key=lambda x: (-len(x), x))[:4]
        removed = sorted(prev - latest, key=lambda x: (-len(x), x))[:3]
        if not previous_text or not latest_text:
            return {
                "label": "New thread",
                "detail": "This is the first captured message in the conversation.",
                "type": "new",
                "added": [],
                "removed": [],
            }
        if not added and not removed:
            return {
                "label": "Context retained",
                "detail": "The latest message keeps the core vocabulary of the previous update.",
                "type": "stable",
                "added": [],
                "removed": [],
            }
        if added:
            detail = "Latest update introduces: " + ", ".join(added) + "."
        else:
            detail = "Latest update is a shorter restatement of the previous context."
        return {
            "label": "Conversation changed",
            "detail": detail,
            "type": "changed",
            "added": added,
            "removed": removed,
        }

    def _thread_state(messages_in_thread, tasks, actions, decisions):
        """Build an explainable state from stored workspace records only."""
        latest = messages_in_thread[-1] if messages_in_thread else None
        now = datetime.now(timezone.utc)

        pending_tasks = [t for t in tasks if (t.status or "pending").lower() == "pending"]
        review_actions = [a for a in actions if (a.status or "").lower() in {"suggested", "approved", "edited"}]
        unresolved_phrases = (
            "waiting", "awaiting", "pending", "need", "needs", "please",
            "confirm", "approval", "approve", "reply", "respond", "send",
            "follow up", "follow-up", "deadline", "due"
        )
        latest_text = (latest.content if latest else "").lower()
        unresolved_signal = any(p in latest_text for p in unresolved_phrases)

        if pending_tasks:
            status = "Action pending"
            status_tone = "amber"
        elif review_actions:
            status = "Review pending"
            status_tone = "violet"
        elif unresolved_signal:
            status = "Needs attention"
            status_tone = "amber"
        elif latest and latest.created_at:
            latest_dt = latest.created_at
            if latest_dt.tzinfo is None:
                latest_dt = latest_dt.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now - latest_dt).total_seconds() / 3600)
            if age_hours <= 24:
                status = "Active"
                status_tone = "green"
            else:
                status = "Quiet"
                status_tone = "slate"
        else:
            status = "Active"
            status_tone = "green"

        scores = [float(d.attention_score) for d in decisions if d.attention_score is not None]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        urgency_high = sum(1 for d in decisions if (d.urgency or "").lower() == "high")
        time_sensitive = sum(1 for d in decisions if bool(d.time_sensitive))
        suspicious = sum(1 for d in decisions if bool(d.suspicious))

        # Transparent "Thread Pulse": a workload/state indicator, not a
        # probability or fabricated ML confidence.
        pulse = avg_score
        pulse += min(12, len(pending_tasks) * 5)
        pulse += min(8, len(review_actions) * 3)
        pulse += min(8, urgency_high * 4)
        pulse += min(6, time_sensitive * 3)
        pulse += min(5, suspicious * 5)
        if unresolved_signal:
            pulse += 7
        pulse = max(0, min(100, round(pulse)))

        if pulse >= 75:
            pulse_label = "High attention"
            pulse_tone = "red"
        elif pulse >= 45:
            pulse_label = "Worth watching"
            pulse_tone = "amber"
        else:
            pulse_label = "Low pressure"
            pulse_tone = "green"

        # Deterministic next move, prioritizing actual stored work.
        if pending_tasks:
            next_move = pending_tasks[0].title
        elif review_actions:
            next_move = review_actions[0].suggestion_text
        elif unresolved_signal:
            next_move = "Review the latest update and respond if it still needs your input."
        elif latest:
            next_move = "No explicit action is pending; keep the thread available for the next update."
        else:
            next_move = "No next move detected."

        # A concise "waiting on" statement is derived from the latest text,
        # not invented as a named person.
        if any(p in latest_text for p in ("approval", "approve", "confirm")):
            waiting_on = "Confirmation / approval"
        elif any(p in latest_text for p in ("reply", "respond", "response")):
            waiting_on = "A response"
        elif any(p in latest_text for p in ("waiting", "awaiting", "pending")):
            waiting_on = "An outstanding response or update"
        else:
            waiting_on = "Nothing explicit detected"

        return {
            "status": status,
            "status_tone": status_tone,
            "pulse": pulse,
            "pulse_label": pulse_label,
            "pulse_tone": pulse_tone,
            "pending_tasks": pending_tasks,
            "review_actions": review_actions,
            "next_move": next_move,
            "waiting_on": waiting_on,
            "avg_score": round(avg_score, 1),
            "urgency_high": urgency_high,
            "time_sensitive": time_sensitive,
            "suspicious": suspicious,
        }

    def _build_thread_intelligence(thread, messages_by_id, tasks_by_message, actions_by_message, decisions_by_message):
        member_messages = [
            messages_by_id[mid] for mid in thread.message_ids if mid in messages_by_id
        ]
        member_messages.sort(key=lambda m: (m.created_at or datetime.min.replace(tzinfo=timezone.utc), m.id))

        thread_tasks = []
        thread_actions = []
        thread_decisions = []
        for m in member_messages:
            thread_tasks.extend(tasks_by_message.get(m.id, []))
            thread_actions.extend(actions_by_message.get(m.id, []))
            thread_decisions.extend(decisions_by_message.get(m.id, []))

        latest = member_messages[-1] if member_messages else None
        previous = member_messages[-2] if len(member_messages) > 1 else None
        state = _thread_state(member_messages, thread_tasks, thread_actions, thread_decisions)
        change = _thread_change_summary(previous.content if previous else "", latest.content if latest else "")

        # Situation statement is deliberately generated from observable data,
        # with no external LLM and no fabricated facts.
        if latest:
            latest_preview = latest.content.strip().replace("\n", " ")
            if len(latest_preview) > 210:
                latest_preview = latest_preview[:207].rstrip() + "..."
            situation = latest_preview
        else:
            situation = "No message context is currently available."

        if len(member_messages) >= 2:
            timeline = []
            for idx, m in enumerate(member_messages):
                if idx == 0:
                    event = "Thread started"
                else:
                    prior = member_messages[idx - 1].content
                    overlap = len(_thread_words(prior) & _thread_words(m.content))
                    event = "Context update" if overlap else "New update"
                timeline.append({
                    "message_id": m.id,
                    "time": m.created_at.strftime("%d %b · %H:%M") if m.created_at else "",
                    "event": event,
                    "content": m.content.strip(),
                    "category": (m.category or "general").title(),
                })
        else:
            timeline = [{
                "message_id": latest.id if latest else None,
                "time": latest.created_at.strftime("%d %b · %H:%M") if latest and latest.created_at else "",
                "event": "Thread started",
                "content": latest.content.strip() if latest else "",
                "category": (latest.category or "general").title() if latest else "General",
            }]

        return {
            "thread": thread,
            "messages": member_messages,
            "situation": situation,
            "change": change,
            "state": state,
            "timeline": timeline,
            "tasks": thread_tasks,
            "actions": thread_actions,
            "decisions": thread_decisions,
            "message_count": len(member_messages),
            "completed_task_count": sum(1 for t in thread_tasks if (t.status or "").lower() == "completed"),
            "review_action_count": len(state["review_actions"]),
        }


    # ========================================================
    # SMART THREADS
    # ========================================================

    @app.route("/smart-threads")
    def smart_threads():
        from models import Thread, Message, Task, Action, Decision

        user = get_current_user()
        messages = Message.query.filter_by(user_id=user.id).order_by(Message.created_at.asc()).limit(500).all()
        owned_ids = {m.id for m in messages}
        messages_by_id = {m.id: m for m in messages}

        # Everything below is scoped through the user's real messages.
        decisions = Decision.query.filter(Decision.message_id.in_(owned_ids)).all() if owned_ids else []
        tasks = Task.query.filter(Task.message_id.in_(owned_ids)).all() if owned_ids else []
        actions = Action.query.filter(Action.message_id.in_(owned_ids)).all() if owned_ids else []

        decisions_by_message = {}
        for d in decisions:
            decisions_by_message.setdefault(d.message_id, []).append(d)
        tasks_by_message = {}
        for t in tasks:
            tasks_by_message.setdefault(t.message_id, []).append(t)
        actions_by_message = {}
        for a in actions:
            actions_by_message.setdefault(a.message_id, []).append(a)

        thread_intelligence = []
        for thread in Thread.query.order_by(Thread.updated_at.desc()).all():
            member_ids = [mid for mid in thread.message_ids if mid in owned_ids]
            if not member_ids:
                continue
            enriched = _build_thread_intelligence(
                thread,
                messages_by_id,
                tasks_by_message,
                actions_by_message,
                decisions_by_message,
            )
            thread_intelligence.append(enriched)

        active_count = sum(1 for item in thread_intelligence if item["state"]["status"] in {"Active", "Action pending", "Review pending", "Needs attention"})
        total_grouped_messages = sum(item["message_count"] for item in thread_intelligence)
        attention_threads = sum(1 for item in thread_intelligence if item["state"]["pulse"] >= 75)
        pending_tasks = sum(len(item["state"]["pending_tasks"]) for item in thread_intelligence)
        waiting_threads = sum(1 for item in thread_intelligence if item["state"]["waiting_on"] != "Nothing explicit detected")

        return render_template(
            "smart_threads.html",
            active="smart_threads",
            thread_intelligence=thread_intelligence,
            active_count=active_count,
            total_grouped_messages=total_grouped_messages,
            attention_threads=attention_threads,
            pending_tasks=pending_tasks,
            waiting_threads=waiting_threads,
        )

    # ========================================================
    # ACTION CENTER
    # ========================================================

    @app.route("/action-center")
    def action_center():
        from models import Action, Message

        action_rows = (
            db.session.query(Action, Message)
            .join(Message, Message.id == Action.message_id)
            .filter(Message.user_id == get_current_user().id)
            .order_by(Action.id.desc())
            .limit(200)
            .all()
        )

        total_actions = len(action_rows)
        pending_actions = sum(
            1 for action, _ in action_rows
            if action.status in ("suggested", "edited")
        )
        approved_actions = sum(
            1 for action, _ in action_rows
            if action.status == "approved"
        )
        executed_actions = sum(
            1 for action, _ in action_rows
            if action.status == "executed"
        )
        dismissed_actions = sum(
            1 for action, _ in action_rows
            if action.status == "dismissed"
        )

        return render_template(
            "action_center.html",
            active="action_center",
            actions=action_rows,
            total_actions=total_actions,
            pending_actions=pending_actions,
            approved_actions=approved_actions,
            executed_actions=executed_actions,
            dismissed_actions=dismissed_actions,
        )

    # ========================================================
    # PROACTIVE BRIEF
    # ========================================================

    @app.route("/proactive-brief")
    def proactive_brief():
        from agent.proactive_brief import generate_proactive_brief

        user = get_current_user()
        try:
            brief = generate_proactive_brief(user_id=user.id)
        except Exception:
            logger.exception("Proactive brief page generation failed")
            brief = {
                "is_empty": True,
                "critical_count": 0,
                "important_count": 0,
                "upcoming_count": 0,
                "safety_count": 0,
                "critical_items": [],
                "important_items": [],
                "upcoming_items": [],
                "safety_items": [],
                "next_move": {
                    "label": "Brief unavailable",
                    "detail": "Refresh the page to recalculate from your current workspace data.",
                    "type": "clear",
                },
            }

        total_attention_items = (
            brief["critical_count"]
            + brief["important_count"]
            + brief["upcoming_count"]
            + brief["safety_count"]
        )

        return render_template(
            "proactive_brief.html",
            active="proactive_brief",
            brief=brief,
            total_attention_items=total_attention_items,
        )

    @app.route("/api/proactive-brief")
    def proactive_brief_api():
        from agent.proactive_brief import generate_proactive_brief
        user = get_current_user()
        return jsonify(generate_proactive_brief(user_id=user.id))

    @app.route("/settings")
    def settings():
        user = get_current_user()
        prefs = get_user_preferences(user.id)
        return render_template(
            "settings.html",
            active="settings",
            categories=ALL_CATEGORIES,
            prefs=prefs,
        )

    @app.route("/settings/preferences", methods=["POST"])
    def update_preferences():
        from models import Preference
        user = get_current_user()
        for category in ALL_CATEGORIES:
            level = request.form.get(f"pref_{category}")
            if not level:
                continue
            pref = Preference.query.filter_by(user_id=user.id, category=category).first()
            if pref:
                pref.priority_level = level
            else:
                pref = Preference(user_id=user.id, category=category, priority_level=level)
                db.session.add(pref)
        db.session.commit()
        return render_template(
            "settings.html",
            active="settings",
            categories=ALL_CATEGORIES,
            prefs=get_user_preferences(user.id),
            saved=True,
        )

    # ---------------- JSON API ----------------

    @app.route("/api/analyze", methods=["POST"])
    def api_analyze():
        from models import Message, Decision, Thread, Task, Action
        from agent.thread_engine import find_matching_thread, make_thread_title
        from agent.action_engine import suggest_actions
        from datetime import datetime, timezone, timedelta

        payload = request.get_json(silent=True) or {}
        raw_message = payload.get("message")

        if raw_message is not None and not isinstance(raw_message, str):
            return jsonify({"error": "message must be a string"}), 400

        text = (raw_message or "").strip()

        if not text:
            return jsonify({"error": "Message content is required"}), 400
        if len(text) > 10000:
            return jsonify({"error": "Message is too long (max 10000 characters)"}), 400

        try:
            user = get_current_user()
            preferences = get_user_preferences(user.id)
            result = analyze_message_v3(text, preferences=preferences, user_id=user.id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception:
            logger.exception("Analysis failed")
            return jsonify({"error": "Internal analysis error"}), 500

        message = Message(user_id=user.id, content=text, category=result["category"])

        try:
            db.session.add(message)
            db.session.flush()

            decision = Decision(
                message_id=message.id,
                attention_score=result["attention_score"],
                decision=result["decision"],
                urgency=result["urgency"],
                relevance=result["relevance"],
                time_sensitive=result["time_sensitive"],
                deadline=result["deadline"],
                action_required=result["action_required"],
                suspicious=result["suspicious"],
                explanation=result["explanation"],
            )
            decision.reasons = result["reasons"]
            db.session.add(decision)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to persist analysis")
            return jsonify({"error": "Could not save analysis result"}), 500

        # ---- V3: Smart Threads, Task Extraction, Action Suggestions ----
        # Deliberately isolated in its own try/except: a bug here must
        # never take down the core V1/V2 analysis+save above, which has
        # already committed successfully by this point.
        matched_thread = None
        created_task_ids = []
        created_action_ids = []
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=45)
            candidate_threads_all = (
                Thread.query.filter(Thread.category == result["category"])
                .filter(Thread.updated_at >= cutoff)
                .order_by(Thread.updated_at.desc())
                .limit(100)
                .all()
            )
            owned_message_ids = {m.id for m in Message.query.filter_by(user_id=user.id).all()}
            candidate_threads = [t for t in candidate_threads_all if any(mid in owned_message_ids for mid in t.message_ids)][:20]
            latest_by_thread = {}
            for t in candidate_threads:
                ids = t.message_ids
                if not ids:
                    continue
                latest_msg = db.session.get(Message, ids[-1])
                if latest_msg:
                    latest_by_thread[t.id] = latest_msg.content

            matched_thread = find_matching_thread(text, result["category"], candidate_threads, latest_by_thread)
            if matched_thread is None:
                matched_thread = Thread(
                    title=make_thread_title(text),
                    category=result["category"],
                    status="active",
                )
                matched_thread.message_ids = [message.id]
                db.session.add(matched_thread)
            else:
                matched_thread.add_message(message.id)
            db.session.commit()

            for task_data in result.get("tasks", []):
                task = Task(
                    message_id=message.id,
                    title=task_data["title"],
                    description=task_data.get("description"),
                    deadline=task_data.get("deadline"),
                    priority=task_data.get("priority", "low"),
                    status="pending",
                )
                db.session.add(task)
                db.session.flush()
                created_task_ids.append(task.id)
            db.session.commit()

            suggestions = suggest_actions(text, result, result.get("tasks", []), matched_thread)
            for s in suggestions:
                action = Action(
                    message_id=message.id,
                    action_type=s["action_type"],
                    suggestion_text=s["suggestion_text"],
                    status="suggested",
                )
                db.session.add(action)
                db.session.flush()
                created_action_ids.append(action.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("V3 thread/task/action persistence failed; core V1/V2 result is still valid")

        response = dict(result)
        response["message_id"] = message.id
        response["thread_id"] = matched_thread.id if matched_thread else None
        response["thread_title"] = matched_thread.title if matched_thread else None
        response["task_ids"] = created_task_ids
        response["action_ids"] = created_action_ids
        return jsonify(response), 201

    @app.route("/api/feedback", methods=["POST"])
    def api_feedback():
        from models import Message, Decision, Feedback

        payload = request.get_json(silent=True) or {}
        message_id = payload.get("message_id")
        feedback_value = payload.get("feedback")

        if feedback_value not in ("correct", "wrong"):
            return jsonify({"error": "feedback must be 'correct' or 'wrong'"}), 400
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return jsonify({"error": "message_id must be a valid integer"}), 400

        message = db.session.get(Message, message_id)
        if not message or message.user_id != get_current_user().id or not message.decision:
            return jsonify({"error": "Message or its decision not found"}), 404

        try:
            entry = Feedback(
                message_id=message.id,
                original_decision=message.decision.decision,
                original_score=message.decision.attention_score,
                feedback=feedback_value,
            )
            db.session.add(entry)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to save feedback")
            return jsonify({"error": "Could not save feedback"}), 500

        return jsonify({"status": "saved", "feedback_id": entry.id}), 201

    @app.route("/api/messages")
    def api_messages():
        from models import Message, Decision
        results = (
            db.session.query(Message, Decision)
            .join(Decision, Decision.message_id == Message.id)
            .filter(Message.user_id == get_current_user().id)
            .order_by(Message.created_at.desc())
            .limit(100)
            .all()
        )
        return jsonify([
            {
                "id": m.id,
                "content": m.content,
                "category": m.category,
                "created_at": m.created_at.isoformat(),
                "attention_score": d.attention_score,
                "decision": d.decision,
                "urgency": d.urgency,
                "reasons": d.reasons,
                "explanation": d.explanation,
            }
            for m, d in results
        ])

    # ---- V3: Action review-first workflow ----
    # Enforced lifecycle: suggested -> approved/edited/dismissed -> executed.
    # Nothing in this codebase can move a row straight from "suggested" to
    # "executed" — /approve, /edit, /dismiss are the only ways out of
    # "suggested", and /execute below explicitly refuses unless the row is
    # already "approved" or "edited". There is no auto-execute path.

    @app.route("/api/actions")
    def api_actions():
        from models import Action, Message
        rows = (db.session.query(Action, Message)
                .join(Message, Message.id == Action.message_id)
                .filter(Message.user_id == get_current_user().id)
                .order_by(Action.id.desc()).limit(200).all())
        return jsonify([{"id": a.id, "status": a.status, "action_type": a.action_type,
                         "suggestion_text": a.suggestion_text, "message_id": m.id,
                         "created_at": a.created_at.isoformat() if a.created_at else None,
                         "updated_at": a.updated_at.isoformat() if a.updated_at else None} for a, m in rows])

    @app.route("/api/actions/<int:action_id>/reopen", methods=["POST"])
    def api_action_reopen(action_id):
        from models import Action, Message
        action = db.session.get(Action, action_id)
        if not action:
            return jsonify({"error": "Action not found"}), 404
        message = db.session.get(Message, action.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Action not found"}), 404
        if action.status == "executed":
            return jsonify({"error": "Executed actions cannot be reopened."}), 400
        action.status = "suggested"
        db.session.commit()
        return jsonify({"id": action.id, "status": action.status}), 200

    @app.route("/api/actions/<int:action_id>/approve", methods=["POST"])
    def api_action_approve(action_id):
        from models import Action
        action = db.session.get(Action, action_id)
        if not action:
            return jsonify({"error": "Action not found"}), 404
        from models import Message
        message = db.session.get(Message, action.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Action not found"}), 404
        if action.status not in ("suggested", "edited"):
            return jsonify({"error": f"Cannot approve an action with status '{action.status}'"}), 400
        action.status = "approved"
        db.session.commit()
        return jsonify({"id": action.id, "status": action.status}), 200

    @app.route("/api/actions/<int:action_id>/edit", methods=["POST"])
    def api_action_edit(action_id):
        from models import Action
        payload = request.get_json(silent=True) or {}
        new_text = payload.get("suggestion_text")
        if not new_text or not isinstance(new_text, str) or not new_text.strip():
            return jsonify({"error": "suggestion_text is required"}), 400

        action = db.session.get(Action, action_id)
        if not action:
            return jsonify({"error": "Action not found"}), 404
        from models import Message
        message = db.session.get(Message, action.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Action not found"}), 404
        if action.status not in ("suggested", "approved", "edited"):
            return jsonify({"error": f"Cannot edit an action with status '{action.status}'"}), 400

        action.suggestion_text = new_text.strip()
        action.status = "edited"
        db.session.commit()
        return jsonify({"id": action.id, "status": action.status, "suggestion_text": action.suggestion_text}), 200

    @app.route("/api/actions/<int:action_id>/dismiss", methods=["POST"])
    def api_action_dismiss(action_id):
        from models import Action
        action = db.session.get(Action, action_id)
        if not action:
            return jsonify({"error": "Action not found"}), 404
        from models import Message
        message = db.session.get(Message, action.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Action not found"}), 404
        if action.status == "executed":
            return jsonify({"error": "Cannot dismiss an already-executed action"}), 400
        action.status = "dismissed"
        db.session.commit()
        return jsonify({"id": action.id, "status": action.status}), 200

    @app.route("/api/actions/<int:action_id>/execute", methods=["POST"])
    def api_action_execute(action_id):
        from models import Action
        action = db.session.get(Action, action_id)
        if not action:
            return jsonify({"error": "Action not found"}), 404
        from models import Message
        message = db.session.get(Message, action.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Action not found"}), 404
        # This is the single enforcement point for the review-first rule:
        # execution is refused unless a human has already approved (or
        # approved-via-edit) this specific action. No code path in this
        # application can reach this branch's success case without that.
        if action.status not in ("approved", "edited"):
            return jsonify({"error": f"Cannot execute an action with status '{action.status}' — it must be approved first"}), 400

        # No real external integration exists yet (no email/calendar/form
        # API is wired up). Execution is therefore local/simulated: the
        # action is marked executed and nothing external happens. The
        # status transition itself is the architecture point future
        # integrations (e.g. actually sending a reply) would hook into.
        action.status = "executed"
        db.session.commit()
        return jsonify({"id": action.id, "status": action.status, "note": "Executed locally (no external integration configured)."}), 200

    @app.route("/api/tasks/<int:task_id>/complete", methods=["POST"])
    def api_task_complete(task_id):
        from models import Task
        from datetime import datetime, timezone
        task = db.session.get(Task, task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404
        from models import Message
        message = db.session.get(Message, task.message_id)
        if not message or message.user_id != get_current_user().id:
            return jsonify({"error": "Task not found"}), 404
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({"id": task.id, "status": task.status}), 200

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception("Server error")
        return jsonify({"error": "Internal server error"}), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.config["DEBUG"])