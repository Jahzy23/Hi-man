/* Our Fresh Air — site scripts */
(function () {
  "use strict";

  // Mobile navigation
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("primary-nav");
  var backdrop = document.querySelector(".nav-backdrop");
  function closeNav() {
    if (!nav) return;
    nav.classList.remove("open");
    document.body.classList.remove("nav-open");
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      document.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    nav.querySelectorAll("a").forEach(function (a) { a.addEventListener("click", closeNav); });
    if (backdrop) backdrop.addEventListener("click", closeNav);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeNav(); });
  }

  // Scroll reveal
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    document.querySelectorAll(".reveal").forEach(function (el) { io.observe(el); });
  } else {
    document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in"); });
  }

  // Current year
  document.querySelectorAll("[data-year]").forEach(function (el) { el.textContent = new Date().getFullYear(); });

  // Quote form
  var form = document.getElementById("quote-form");
  if (form) {
    var status = document.getElementById("form-status");
    var endpoint = form.getAttribute("data-endpoint") || "";
    var mailTo = form.getAttribute("data-mailto") || "";

    function show(kind, msg) {
      status.className = "form-status " + kind;
      status.textContent = msg;
      status.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (form.querySelector('[name="company_website"]').value) return; // honeypot
      if (!form.checkValidity()) { form.reportValidity(); return; }

      var data = new FormData(form);
      var btn = form.querySelector('button[type="submit"]');
      var original = btn.textContent;
      btn.disabled = true; btn.textContent = "Sending…";

      // Preferred: POST to a form endpoint (Formspree, Netlify, Basin, your own API, etc.)
      if (endpoint) {
        fetch(endpoint, { method: "POST", body: data, headers: { Accept: "application/json" } })
          .then(function (r) {
            if (r.ok) { form.reset(); show("ok", "Thank you! Your request was received. We will contact you shortly to schedule your free inspection."); }
            else { throw new Error("bad status"); }
          })
          .catch(function () {
            show("err", "Sorry, something went wrong sending your request. Please call us at 786-369-9016 or email Jonathan@OurFreshAir.com.");
          })
          .finally(function () { btn.disabled = false; btn.textContent = original; });
        return;
      }

      // Fallback: open the visitor's email client with the request pre-filled.
      var lines = [];
      data.forEach(function (v, k) { if (k !== "company_website" && v) lines.push(k.replace(/_/g, " ") + ": " + v); });
      var subject = encodeURIComponent("Free quote request — " + (data.get("name") || "website"));
      var body = encodeURIComponent(lines.join("\n"));
      window.location.href = "mailto:" + mailTo + "?subject=" + subject + "&body=" + body;
      show("ok", "Your email app should open with the request pre-filled. If it does not, call 786-369-9016 or email Jonathan@OurFreshAir.com.");
      btn.disabled = false; btn.textContent = original;
    });
  }
})();
