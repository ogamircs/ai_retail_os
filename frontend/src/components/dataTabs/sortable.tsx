import { useMemo, useState } from "react";

export type SortDir = "asc" | "desc";

export function useSort<T>(rows: T[], initialKey: keyof T, initialDir: SortDir = "desc") {
  const [key, setKey] = useState<keyof T>(initialKey);
  const [dir, setDir] = useState<SortDir>(initialDir);

  const sorted = useMemo(() => {
    const factor = dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      if (av === bv) return 0;
      if (av == null) return -1 * factor;
      if (bv == null) return 1 * factor;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }, [rows, key, dir]);

  function header(name: keyof T, label: string, extraClass = "") {
    const active = name === key;
    return {
      onClick: () => {
        if (name === key) setDir((d) => (d === "asc" ? "desc" : "asc"));
        else {
          setKey(name);
          setDir("desc");
        }
      },
      className: `${extraClass} ${active ? `active ${dir}` : ""}`.trim(),
      children: (
        <>
          {label} {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </>
      ),
    };
  }

  return { sorted, key, dir, header };
}
