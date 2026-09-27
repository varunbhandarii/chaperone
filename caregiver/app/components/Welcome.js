import { useState } from "react";
import Icon from "./Icon";
import Message from "./Message";

// Six drawn boxes over one real input, so the phone's one-time-code autofill and paste both work.
function SetupCode({ value, onChange }) {
  const [focused, setFocused] = useState(false);
  const digits = value.split("");
  const active = Math.min(digits.length, 5);
  return (
    <div className="cg-otp">
      {[0, 1, 2, 3, 4, 5].map((index) => (
        <span key={index} className={`cg-otp__box${focused && index === active ? " cg-otp__box--on" : ""}`} aria-hidden="true">
          {digits[index] || ""}
        </span>
      ))}
      <input
        id="setup-code"
        className="cg-otp__input"
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/\D/g, "").slice(0, 6))}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={6}
        aria-describedby="setup-code-help"
      />
    </div>
  );
}

export default function Welcome({ setupCode, onSetupCode, onRegister, onSignIn, message, onDismiss }) {
  return (
    <div className="cg-welcome">
      <main className="cg-welcome__inner">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="cg-welcome__logo" src="/design/logo.svg" alt="Chaperone" height={34} />
        <Message message={message} onDismiss={onDismiss} />
        <div>
          <h1 className="cg-welcome__title">Set up Chaperone for Ruth</h1>
          <p className="cg-welcome__lead">You&apos;ll sign Ruth&apos;s rules with a passkey on this phone. There is no password to remember.</p>
        </div>
        <ol className="cg-steps">
          <li className="cg-step">
            <span className="cg-step__num cg-step__num--on" aria-hidden="true">1</span>
            <div className="cg-step__body">
              <label className="cg-step__title" htmlFor="setup-code">Enter the setup code</label>
              <SetupCode value={setupCode} onChange={onSetupCode} />
              <p id="setup-code-help" className="cg-field__help">Six digits, shown when Chaperone starts. Good for 10 minutes.</p>
            </div>
          </li>
          <li className="cg-step">
            <span className="cg-step__num" aria-hidden="true">2</span>
            <div className="cg-step__body">
              <p className="cg-step__title">Create your passkey</p>
              <p className="cg-step__text">Your face, fingerprint or phone PIN. It never leaves this phone.</p>
            </div>
          </li>
          <li className="cg-step">
            <span className="cg-step__num" aria-hidden="true">3</span>
            <div className="cg-step__body">
              <p className="cg-step__title">Sign Ruth&apos;s rules together</p>
              <p className="cg-step__text">Ruth hears them in her language and says yes.</p>
            </div>
          </li>
        </ol>
        <div className="cg-welcome__grow" />
        <div className="cg-btn-col">
          <button type="button" className="ch-btn ch-btn--primary cg-btn cg-btn--tall" onClick={onRegister}>
            <Icon name="key" size={20} />Create your passkey
          </button>
          <button type="button" className="ch-btn cg-btn cg-btn--tall" onClick={onSignIn}>I already have one · Sign in</button>
          <p className="cg-welcome__foot">Visa sandbox · no real money moves</p>
        </div>
      </main>
    </div>
  );
}
