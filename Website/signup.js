window.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".signup-form");

  form.addEventListener("submit", async function (e) {
    e.preventDefault();

    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;

    if (!username || !password) {
      alert("Please fill all fields!");
      return;
    }

    const res = await fetch("/api/signup", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ username, password })
    });

    const data = await res.json();

    if (data.success) {
      alert("Account created successfully!");
      window.location.href = "/login";
    } else {
      alert(data.error);
    }
  });
});