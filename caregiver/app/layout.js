export const metadata = { title: "Chaperone caregiver" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "Georgia, serif", margin: "1.5rem", background: "#f6f1e7", color: "#1c140c" }}>
        {children}
      </body>
    </html>
  );
}
