import { Card, CardContent } from "@/components/ui/card";
import { useI18n } from "@/i18n";

/** Shared loading / error / empty placeholders, all drawing from i18n. */

export function LoadingView() {
  const { t } = useI18n();
  return <p className="py-10 text-center text-sm text-muted-foreground">{t('common.loading')}</p>;
}

export function ErrorView({ message }: { message: string }) {
  const { t } = useI18n();
  return (
    <Card className="border-destructive/40">
      <CardContent className="px-4 py-3 text-sm" role="alert">
        <span className="font-medium text-destructive">{t('common.error')}</span>
        <span className="ml-2 text-muted-foreground">
          {t('common.request_failed')} — {message}
        </span>
      </CardContent>
    </Card>
  );
}

export function EmptyView() {
  const { t } = useI18n();
  return <p className="py-8 text-center text-sm text-muted-foreground">{t('common.empty')}</p>;
}
