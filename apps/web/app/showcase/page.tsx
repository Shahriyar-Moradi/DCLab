import { ShowcaseCatalog } from "@/app/showcase/ShowcaseCatalog";
import { notFound } from "next/navigation";

export default function ShowcasePage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <ShowcaseCatalog />;
}
