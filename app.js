// VYRA shared front-end utilities.
// Page-specific interactivity (e.g. the Analyze flow) lives inline in its template
// since it's tightly coupled to that page's DOM. This file is reserved for
// cross-page behavior shared by every page.

document.addEventListener('DOMContentLoaded', () => {
  // Populate the top bar with the real current date, computed from the
  // browser's own clock. This is genuine, unfabricated data — VYRA V1 has
  // no server-side "current date" concept to pass down, so this stays
  // client-side rather than inventing a backend endpoint for it.
  const dateEl = document.getElementById('topbarDate');
  if (dateEl) {
    const now = new Date();
    const formatted = now.toLocaleDateString(undefined, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    });
    dateEl.textContent = formatted;
  }
});