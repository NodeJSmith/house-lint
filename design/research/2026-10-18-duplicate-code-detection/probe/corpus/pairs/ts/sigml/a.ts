export function alphaCard({
  entity,
  config,
  hass,
  onSelect,
  className,
  compact,
}: CardProps): CardModel {
  const name = config.name ?? entity.attributes.friendly_name;
  const active = entity.state === "on";
  return buildModel(className, name, active, () => onSelect(entity.entity_id));
}
