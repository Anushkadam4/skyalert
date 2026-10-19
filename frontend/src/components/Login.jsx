import { useState } from "react";
import { api } from "../api.js";

export default function Login({ onLogin }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const isLogin = mode === "login";

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { access_token } = await api(`/auth/${mode}`, { method: "POST", body: { email, password }, token: null });
      onLogin(access_token);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="panel">
        <div>
          <h1>SkyAlert</h1>
          <p className="muted">Get notified when something new appears in the part of the sky you care about.</p>
        </div>
        <form onSubmit={submit}>
          <label>
            Email
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          </label>
          <label>
            Password
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isLogin ? "current-password" : "new-password"}
            />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="primary" disabled={busy}>
            {isLogin ? "Log in" : "Create account"}
          </button>
        </form>
        <p className="small muted">
          {isLogin ? "No account yet? " : "Already registered? "}
          <button className="switch" onClick={() => setMode(isLogin ? "register" : "login")}>
            {isLogin ? "Create one" : "Log in"}
          </button>
        </p>
      </div>
    </div>
  );
}
