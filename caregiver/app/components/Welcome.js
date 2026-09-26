import { btn, field } from "./styles";

export default function Welcome({ setupCode, onSetupCode, onRegister, onSignIn }) {
  return (
    <section>
      <h1>Set up Chaperone for Ruth</h1>
      <p style={{ fontSize: "1.15rem" }}>Enter the setup code, create your passkey, then sign in.</p>
      <input value={setupCode} onChange={(event) => onSetupCode(event.target.value)} inputMode="numeric" placeholder="setup code" style={field} />
      <p>
        <button style={btn} onClick={onRegister}>Create your passkey</button>
        <button style={btn} onClick={onSignIn}>Sign in</button>
      </p>
    </section>
  );
}
