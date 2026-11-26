document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".version-switcher__button").forEach((button) => {
    const updateAccessibleName = () => {
      const visibleLabel = button.textContent.replace(/\s+/g, " ").trim();
      if (visibleLabel) button.setAttribute("aria-label", `Documentation version: ${visibleLabel}`);
    };
    new MutationObserver(updateAccessibleName).observe(button, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    updateAccessibleName();
  });

  document.querySelectorAll(".pst-scrollable-table-container > table").forEach((table) => {
    table.setAttribute("tabindex", "0");
  });

  document.querySelectorAll(".sphx-glr-thumbcontainer").forEach((card) => {
    const link = card.querySelector("a.reference.internal");
    const title = card.querySelector(".sphx-glr-thumbnail-title")?.textContent?.trim();
    if (link && title && !link.hasAttribute("aria-label")) {
      link.setAttribute("aria-label", `Open example: ${title}`);
    }
  });
});
