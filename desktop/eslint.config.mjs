import js from "@eslint/js"
import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", ".electron/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["scripts/**/*.mjs", "*.mjs"],
    languageOptions: { globals: { console: "readonly", process: "readonly" } },
  },
)
