import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { ApiError } from "@/api/client";
import { useToast } from "@/components/Toast";

interface HasId {
  id: number;
}

// Optimistic delete for an item that lives in a list query. The row vanishes
// immediately (no reload, scroll preserved); on error we roll back and toast.
export function useDeleteMutation<T extends HasId>(opts: {
  listKey: QueryKey;
  mutationFn: (id: number) => Promise<unknown>;
  successMessage?: (id: number) => string;
  onSuccess?: (id: number) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (id: number) => opts.mutationFn(id),
    onMutate: async (id: number) => {
      await qc.cancelQueries({ queryKey: opts.listKey });
      const prev = qc.getQueryData<T[]>(opts.listKey);
      qc.setQueryData<T[]>(opts.listKey, (old) => old?.filter((item) => item.id !== id));
      return { prev };
    },
    onError: (error, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(opts.listKey, ctx.prev);
      toast.error(error instanceof ApiError ? error.message : "Не удалось удалить");
    },
    onSuccess: (_data, id) => {
      if (opts.successMessage) toast.success(opts.successMessage(id));
      opts.onSuccess?.(id);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: opts.listKey });
    },
  });
}
