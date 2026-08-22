export function nextActiveAfterDelete(
  deletedId: string,
  conversations: { id: string }[],
  activeId: string | null,
): string | null {
  const remaining = conversations.filter((item) => item.id !== deletedId)
  if (activeId !== deletedId) return activeId
  return remaining[0]?.id ?? null
}
