interface SimulateBubbleProps {
  label: string;
  onSimulate: () => void;
}

export function SimulateBubble({ label, onSimulate }: SimulateBubbleProps) {
  return (
    <button type="button" className="simulate-bubble" onClick={onSimulate}>
      {label}
    </button>
  );
}
