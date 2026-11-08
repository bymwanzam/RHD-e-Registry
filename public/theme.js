// Applies the saved colour theme before the page is drawn, so there is no flash of the wrong theme.
// Dark is the default; the choice is remembered per browser (toggle in the sidebar or on the sign-in page).
(function () {
  var theme = 'dark';
  try {
    var saved = localStorage.getItem('ereg-theme');
    if (saved === 'light' || saved === 'dark') theme = saved;
  } catch (e) { /* storage blocked: use the default */ }
  document.documentElement.setAttribute('data-theme', theme);
})();
