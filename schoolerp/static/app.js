// No inline scripts are allowed by our Content-Security-Policy, so behaviour lives here.
document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("all-present");
  if (btn) btn.addEventListener("click", function () {
    document.querySelectorAll('.radios input[value="present"]').forEach(function (r) { r.checked = true; });
  });
  var sel = document.getElementById("session_status"), box = document.getElementById("students-box");
  if (sel && box) {
    var toggle = function () {
      var held = sel.value === "held";
      box.classList.toggle("hidden", !held);
      box.querySelectorAll("input[type=radio]").forEach(function (r) { r.required = held; });
    };
    sel.addEventListener("change", toggle); toggle();
  }
});
