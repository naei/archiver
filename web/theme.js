// Applies the saved theme before the page renders, to avoid a flash of the other theme.
try { document.documentElement.dataset.theme = localStorage.getItem("theme") || "dark"; } catch (error) { /* storage unavailable */ }
// Tells the browser which scheme to paint with before the stylesheet arrives, and the colour of its own bars.
document.querySelector('meta[name="color-scheme"]').content = document.documentElement.dataset.theme === "light" ? "light" : "dark";
document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => { meta.content = document.documentElement.dataset.theme === "light" ? "#fafbfc" : "#14161c"; });
