import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

export function useViewportFillPanel<T extends HTMLElement = HTMLElement>(trigger: unknown) {
  const ref = useRef<T | null>(null);
  const [height, setHeight] = useState<number | undefined>();

  useLayoutEffect(() => {
    let frame = 0;
    const update = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const node = ref.current;
        if (!node) return;
        const bottomInset = 24;
        const availableHeight = Math.floor(window.innerHeight - node.getBoundingClientRect().top - bottomInset);
        setHeight(Math.max(160, availableHeight));
      });
    };

    update();
    window.addEventListener("resize", update);

    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(update);
    if (ref.current?.parentElement) observer?.observe(ref.current.parentElement);

    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", update);
      observer?.disconnect();
    };
  }, [trigger]);

  return {
    ref,
    style: height === undefined ? undefined : ({ height: `${height}px` } as CSSProperties)
  };
}
