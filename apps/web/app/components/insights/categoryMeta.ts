import type { InsightCategoryValue } from "@/lib/domain";
import { Gem, HeartHandshake, Megaphone, Sparkles, TrendingUp, Wallet, type LucideIcon } from "lucide-react";

export const CATEGORY_META: Record<InsightCategoryValue, { icon: LucideIcon; blurb: string }> = {
  Marketing: { icon: Megaphone, blurb: "Who is engaging, and who is worth reaching next." },
  Sales: { icon: TrendingUp, blurb: "Which prospects and leads are worth pursuing right now." },
  Revenue: { icon: Wallet, blurb: "Where to expand or bundle for the customers most likely to say yes." },
  "Churn & Retention": { icon: HeartHandshake, blurb: "Who is at risk of leaving, and what wins them back." },
  "Customer Value": { icon: Gem, blurb: "Which accounts carry the most long-term value." },
  Custom: { icon: Sparkles, blurb: "Insights from workspace-specific prototypes." },
};

// Rendered strictly in the order the client should think about their business,
// not by prediction type — see apps/api/app/translation/models.py::InsightCategory.
export const CATEGORY_ORDER: InsightCategoryValue[] = [
  "Marketing",
  "Sales",
  "Revenue",
  "Churn & Retention",
  "Customer Value",
  "Custom",
];
