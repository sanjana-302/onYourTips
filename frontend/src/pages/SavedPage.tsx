import { api } from "../api/client";
import { ErrorNotice, LoadingRows, Notice, PageHeader } from "../components/common";
import { ItemList } from "../components/ItemRow";
import { useApp } from "../lib/AppContext";
import { useAsync } from "../lib/useAsync";

export function SavedPage({ active }: { active: boolean }) {
  const { dataVersion, stateOf } = useApp();
  // Refetch whenever the tab is opened, so items saved elsewhere show up.
  const saved = useAsync(() => api.saved(), [active, dataVersion]);
  const items = (saved.data?.items ?? []).filter((i) => stateOf(i).saved);

  return (
    <>
      <PageHeader
        title="Saved"
        subtitle={items.length ? `${items.length} to read or build on` : "Your reading list"}
      />
      {saved.error ? <ErrorNotice error={saved.error} onRetry={saved.reload} /> : null}
      {saved.loading && !saved.data ? <LoadingRows /> : null}
      {saved.data && items.length === 0 ? (
        <Notice title="Nothing saved yet">
          Tap Save on any story to keep it here for later. Saves also teach the app what you like.
        </Notice>
      ) : null}
      {items.length > 0 ? <ItemList items={items} tab="saved" /> : null}
    </>
  );
}
