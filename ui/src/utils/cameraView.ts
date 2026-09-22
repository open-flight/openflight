export function verticalViewTargets(offset: number, step: number, rotate180: boolean) {
  const upDelta = rotate180 ? step : -step;
  return { up: offset + upDelta, down: offset - upDelta };
}
