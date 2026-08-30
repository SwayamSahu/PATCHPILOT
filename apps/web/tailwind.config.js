/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0a0b0d",
        panel: "#121317",
        edge: "#23252c",
        muted: "#8b8f9a",
        accent: "#e5b93c",
        ok: "#3fb950",
        warn: "#d29922",
        bad: "#f85149",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
