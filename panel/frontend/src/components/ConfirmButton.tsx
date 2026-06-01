import { useEffect, useState, type ReactNode } from 'react';
import { cx } from '../utils';

export function ConfirmButton({
  children,
  confirmText = '确认？',
  className = 'danger',
  onConfirm,
  title,
}: {
  children: ReactNode;
  confirmText?: string;
  className?: string;
  onConfirm: () => void | Promise<void>;
  title?: string;
}) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), 3000);
    return () => window.clearTimeout(timer);
  }, [armed]);

  async function click() {
    if (!armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    await onConfirm();
  }

  return (
    <button className={cx(className, armed && 'confirming')} onClick={click} title={title || confirmText}>
      {armed ? confirmText : children}
    </button>
  );
}
