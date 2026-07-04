import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        input: { DEFAULT: "#3aa7ff", light: "#70c0ff", dark: "#2f80ed", muted: "#183d59" },
        output: { DEFAULT: "#2fbf71", light: "#56d68f", dark: "#219653", muted: "#153d29" },
        surface: { DEFAULT: "#11110f", card: "#181816", border: "#34342f" },
      },
      fontFamily: { mono: ["'JetBrains Mono'", "monospace"] },
    },
  },
} satisfies Config;
