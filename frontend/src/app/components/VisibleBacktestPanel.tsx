import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

const PanelVisibility = createContext(true);
export const usePanelVisibility = () => useContext(PanelVisibility);

export function VisibleBacktestPanel({ children, onVisibility }: { children: ReactNode; onVisibility: (visible: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const callback = useRef(onVisibility);
  callback.current = onVisibility;
  const [visible, setVisible] = useState(false);
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      setVisible(entry.isIntersecting);
      if (entry.isIntersecting) setMounted(true);
      callback.current(entry.isIntersecting);
    });
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return <div ref={ref} style={{ height: "100%", minHeight: 0 }}><PanelVisibility.Provider value={visible}>{mounted ? children : null}</PanelVisibility.Provider></div>;
}
