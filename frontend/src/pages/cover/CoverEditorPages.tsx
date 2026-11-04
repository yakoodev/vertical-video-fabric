import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { ownerCoverApi, sharedCoverApi } from "@/api/coverDocs";
import { CoverEditor } from "./CoverEditor";

export function OwnerCoverEditor() {
  const { docId } = useParams();
  const id = Number(docId);
  const api = useMemo(() => ownerCoverApi(id), [id]);
  return <CoverEditor api={api} docId={id} />;
}

/** Страница ссылки: только адреса этого токена, без оболочки сервиса. */
export function SharedCoverEditor() {
  const { token = "" } = useParams();
  const api = useMemo(() => sharedCoverApi(token), [token]);
  return <CoverEditor api={api} />;
}
