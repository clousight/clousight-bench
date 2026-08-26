import { Badge } from "@/components/ui/badge";
import { useI18n } from "@/i18n";

/** Status pill: completed=green, failed/invalid=red, anything else=gray. */
export function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  if (status === "completed") return <Badge variant="success">{t('status.completed')}</Badge>;
  if (status === "failed") return <Badge variant="destructive">{t('status.failed')}</Badge>;
  if (status === "invalid") return <Badge variant="destructive">{t('status.invalid')}</Badge>;
  return <Badge variant="secondary">{status || t('status.other')}</Badge>;
}
