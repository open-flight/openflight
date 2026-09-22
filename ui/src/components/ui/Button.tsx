import type { ReactNode } from 'react';
import './Button.css';

export function Button({
  children,
  onClick,
  variant = 'primary',
  type = 'button',
  className,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'ghost';
  type?: 'button' | 'submit';
  className?: string;
}) {
  const classes = ['ui-button', `ui-button--${variant}`];
  if (className) {
    classes.push(className);
  }

  return (
    <button type={type} className={classes.join(' ')} onClick={onClick}>
      {children}
    </button>
  );
}
