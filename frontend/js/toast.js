// One shared toast element on <body>; it outlives view changes so a notice
// raised while navigating (e.g. back to the grid) is still shown.
let node = null;
let timer = null;

export function showToast(message, ms = 3200) {
  if (!node) {
    node = document.createElement("div");
    node.className = "toast";
    node.setAttribute("role", "status");
    document.body.append(node);
  }
  node.textContent = message;
  // Force the transition to replay when a toast is already showing.
  node.classList.remove("show");
  void node.offsetWidth;
  node.classList.add("show");
  clearTimeout(timer);
  timer = setTimeout(() => node.classList.remove("show"), ms);
}
