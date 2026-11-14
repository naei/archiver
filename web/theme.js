// Applies the saved theme before the page renders, to avoid a flash of the other theme.
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch (error) { /* storage unavailable */ }
