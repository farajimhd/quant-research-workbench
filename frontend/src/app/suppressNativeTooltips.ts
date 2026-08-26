const ACCESSIBLE_NAME_TARGETS = [
  "abbr",
  "a",
  "button",
  "iframe",
  "input",
  "select",
  "summary",
  "textarea",
  "[role='button']",
  "[role='menuitem']",
  "[role='option']",
  "[role='tab']",
].join(",");

function suppressTitle(element: Element) {
  const hint = element.getAttribute("title")?.trim();
  if (!hint) {
    element.removeAttribute("title");
    return;
  }

  const needsAccessibleName = element.matches(ACCESSIBLE_NAME_TARGETS)
    && !element.getAttribute("aria-label")
    && !element.getAttribute("aria-labelledby")
    && !element.textContent?.trim();
  if (needsAccessibleName) element.setAttribute("aria-label", hint);
  else if (!element.getAttribute("aria-description")) element.setAttribute("aria-description", hint);
  element.removeAttribute("title");
}

function suppressTree(root: ParentNode) {
  if (root instanceof Element && root.hasAttribute("title")) suppressTitle(root);
  root.querySelectorAll?.("[title]").forEach(suppressTitle);
}

/**
 * Native HTML title tooltips are browser-owned black surfaces that cannot use
 * the application theme. Keep their content available to assistive technology
 * while preventing that parallel tooltip system from rendering anywhere.
 */
export function installNativeTooltipSuppression(root: Document = document) {
  suppressTree(root);

  const suppressClosestTitle = (event: Event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const titled = target.closest("[title]");
    if (titled) suppressTitle(titled);
  };
  root.addEventListener("pointerover", suppressClosestTitle, true);
  root.addEventListener("focusin", suppressClosestTitle, true);

  const observer = new MutationObserver((records) => {
    records.forEach((record) => {
      if (record.type === "attributes" && record.target instanceof Element) {
        suppressTitle(record.target);
        return;
      }
      record.addedNodes.forEach((node) => {
        if (node instanceof Element) suppressTree(node);
      });
    });
  });
  observer.observe(root.documentElement, {
    attributeFilter: ["title"],
    attributes: true,
    childList: true,
    subtree: true,
  });

  return () => {
    observer.disconnect();
    root.removeEventListener("pointerover", suppressClosestTitle, true);
    root.removeEventListener("focusin", suppressClosestTitle, true);
  };
}
