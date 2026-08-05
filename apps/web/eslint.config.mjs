import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

const eslintConfig = [...compat.extends("next").map((config) => ({
  ...config,
  ignores: [".next/**", "node_modules/**"],
}))];

export default eslintConfig;
