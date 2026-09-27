import "./app.css";

export const metadata = {
  title: "Chaperone for Priyank",
  description: "Approve Ruth's purchases, see what Chaperone stopped, and sign her rules with your passkey.",
  icons: { icon: [{ url: "/design/favicon.svg", type: "image/svg+xml" }] },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#1434CB",
  colorScheme: "light",
};

// The shared tokens (and the fonts they load) are copied from design/ into public/design before dev and build.
export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="stylesheet" href="/design/tokens.css" />
      </head>
      <body>{children}</body>
    </html>
  );
}
