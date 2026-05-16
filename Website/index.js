window.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".signin-form");

  form.addEventListener("submit", async function (e) {
    e.preventDefault();

    const username = document.getElementById("email").value;
    const password = document.getElementById("password").value;

    const res = await fetch("/api/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ username, password })
    });

    const data = await res.json();

    if (data.success) {
      window.location.href = "/home";
    } else {
      alert(data.error);
    }
  });
});