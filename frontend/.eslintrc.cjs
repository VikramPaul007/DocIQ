module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  extends: ["eslint:recommended", "plugin:@typescript-eslint/recommended", "plugin:react/recommended"],
  parser: "@typescript-eslint/parser",
  parserOptions: { ecmaVersion: "latest", sourceType: "module" },
  plugins: ["react", "react-hooks", "@typescript-eslint"],
  settings: { react: { version: "detect" } },
  ignorePatterns: ["dist", "node_modules", "*.cjs", "*.config.ts", "*.config.js"],
  rules: {
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "warn",
    "react/react-in-jsx-scope": "off",
    // LLM-generated text (summaries, prompts) must never be rendered as HTML.
    "react/no-danger": "error",
  },
};
