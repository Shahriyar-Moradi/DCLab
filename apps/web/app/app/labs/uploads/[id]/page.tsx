import { redirect } from "next/navigation";

export default function ClientLabUploadRedirect({ params }: { params: { id: string } }) {
  redirect(`/lab/runs/${params.id}`);
}
