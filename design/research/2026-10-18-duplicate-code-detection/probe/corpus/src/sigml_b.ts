export function betaCard({
  entity,
  config,
  hass,
  onSelect,
  className,
  compact,
}: CardProps): CardModel {
  const history = loadHistory(hass, entity.entity_id, config.hours);
  if (history.loading) {
    return spinnerModel(compact);
  }
  return sparklineModel(history.points, className);
}
