import { useLaunchDaddy } from './useLaunchDaddy';

export function LaunchDaddyBrand() {
  const { isLaunchDaddyMode, toggleLaunchDaddy } = useLaunchDaddy();

  if (!isLaunchDaddyMode) return null;

  return (
    <div className="launch-daddy-brand" onClick={toggleLaunchDaddy}>
      <span className="launch-daddy-brand__icon">🔥</span>
      <span className="launch-daddy-brand__text">Launch Daddy</span>
    </div>
  );
}
