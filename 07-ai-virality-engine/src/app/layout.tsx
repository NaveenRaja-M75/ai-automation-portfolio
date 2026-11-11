import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "AI Virality Engine | Creator Intelligence",
  description: "A strategic AI intelligence system for creator growth.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark h-full antialiased" suppressHydrationWarning>
      <body className={`${inter.variable} ${outfit.variable} font-sans min-h-full flex flex-col bg-background text-foreground`} suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
