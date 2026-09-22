import type { ButtonHTMLAttributes, ReactNode } from 'react';

export type PanelActionVariant = 'primary' | 'secondary' | 'danger';

interface PanelActionProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: PanelActionVariant;
}

/**
 * Header (and dialog) actions. Only these three variants exist — do not add
 * chips, ghost, or accent styles in `PanelHeader` actions.
 */
export function PanelAction({ children, variant = 'primary', className, type = 'button', ...props }: PanelActionProps) {
  const classes = ['panel-action', `panel-action--${variant}`];
  if (className) {
    classes.push(className);
  }

  return (
    <button type={type} className={classes.join(' ')} {...props}>
      {children}
    </button>
  );
}
