export const metadata = { title: "Chaperone caregiver" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="stylesheet" href="/design/tokens.css" />
      </head>
      <body style={{ fontFamily: "var(--ch-font, Georgia, serif)", fontSize: "var(--font-base, 20px)", margin: "1.5rem", background: "var(--ch-bg, #f6f1e7)", color: "var(--ch-text, #1b2a4a)" }}>
        {children}
      </body>
    </html>
  );
}
