document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".sphx-glr-thumbcontainer").forEach((card) => {
    const link = card.querySelector("a.reference.internal");
    const title = card.querySelector(".sphx-glr-thumbnail-title")?.textContent?.trim();
    if (link && title && !link.hasAttribute("aria-label")) {
      link.setAttribute("aria-label", `Open example: ${title}`);
    }
  });
});
