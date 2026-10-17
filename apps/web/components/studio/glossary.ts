/**
 * Shared glossary (V7-A1): one plain-words definition per data-science term, used by every Studio tooltip.
 * Pure data, no React. Definitions are plain text (the Term component never renders HTML).
 * Words follow the v6/v7 table: "final test set (used once)", never "holdout".
 */
export type GlossaryKey =
  | "cv" | "finalTest" | "prauc" | "recall" | "precision" | "threshold"
  | "classWeights" | "baseline" | "leakage" | "drift" | "psi"
  | "lightgbm" | "catboost" | "fold" | "trustChecks" | "accessToken";

export type GlossaryEntry = { label: string; definition: string };

export const GLOSSARY: Record<GlossaryKey, GlossaryEntry> = {
  cv: {
    label: "cross-validation",
    definition: "The training rows are cut into folds. Each fold is left out once while the model learns from the others, so every score is on rows the model has not seen. Models are compared on these scores.",
  },
  finalTest: {
    label: "final test set (used once)",
    definition: "Rows set aside before any modelling. Each run scores them once, for that run's chosen model only. They are never used to pick a model, its settings or its threshold, so never compare final-test scores across runs to pick one.",
  },
  prauc: {
    label: "PR-AUC",
    definition: "Area under the precision-recall curve. It summarizes how well the model ranks the rare class (for example churners) at every threshold. Higher is better; a dummy model scores about the share of the rare class.",
  },
  recall: {
    label: "recall",
    definition: "Of the customers who really are positive (for example will cancel), the share the model catches. 0.80 means 8 out of 10 are found.",
  },
  precision: {
    label: "precision",
    definition: "Of the customers the model flags, the share that really are positive. 0.63 means about 6 out of 10 flagged customers are right.",
  },
  threshold: {
    label: "threshold",
    definition: "The probability above which a row is flagged as positive. A lower threshold catches more positives but raises false alarms. It is chosen on cross-validation, never on the final test set.",
  },
  classWeights: {
    label: "class weights",
    definition: "Extra weight given to the rare class while the model learns, so it is not ignored. Balanced weights make both classes count equally.",
  },
  baseline: {
    label: "baseline",
    definition: "A dummy model that ignores the columns (for example always predicts the most common answer). A real model has to beat it by a clear margin to be worth using.",
  },
  leakage: {
    label: "leakage",
    definition: "A column that gives away the answer because it is only known after the outcome. It makes scores look better than they will be on new data.",
  },
  drift: {
    label: "drift",
    definition: "New data looks different from the data the model was trained on (for example a new plan type). Predictions get less reliable as drift grows.",
  },
  psi: {
    label: "PSI",
    definition: "Population Stability Index: a number for how much a column's values shifted between two periods. Below 0.1 is small; 0.2 or more is usually worth a look.",
  },
  lightgbm: {
    label: "LightGBM",
    definition: "A fast model family that adds many small decision trees one after another. It often works well on tables of numbers and categories.",
  },
  catboost: {
    label: "CatBoost",
    definition: "A model family built from decision trees that handles category columns (such as a plan type) directly, without turning them into numbers first.",
  },
  fold: {
    label: "fold",
    definition: "One slice of the training rows in cross-validation. Each fold is left out once and scored by a model that learned from the other folds.",
  },
  trustChecks: {
    label: "trust checks",
    definition: "Automatic checks on every run, such as leakage, a big gap between training and cross-validation scores, duplicate rows, a rare class and scores that look too good to be true. Each one passes or warns.",
  },
  accessToken: {
    label: "access token",
    definition: "A secret key that lets your own code or a coding assistant use DCLab with limited rights. It is shown once, and you can revoke it at any time under Connect.",
  },
};

/** Every term in the order the glossary panel lists them (the 16 terms of the v7 glossary). */
export const GLOSSARY_ORDER: readonly GlossaryKey[] = [
  "cv", "prauc", "recall", "precision", "threshold", "classWeights", "baseline", "leakage",
  "drift", "psi", "lightgbm", "catboost", "fold", "trustChecks", "accessToken", "finalTest",
];

export function glossaryEntry(key: GlossaryKey): GlossaryEntry {
  return GLOSSARY[key];
}
