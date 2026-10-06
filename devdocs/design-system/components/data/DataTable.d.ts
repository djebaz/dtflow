import * as React from "react";

export interface DataTableProps {
  columns: { key: string; label: string; mono?: boolean }[];
  rows: Record<string, React.ReactNode>[];
  selectedIndex?: number;
  /** "gallery": surface fill with an accentHover outline on hover, matching ImageCard and Thumbnail. */
  variant?: "default" | "gallery";
  onSelect?: (index: number) => void;
}
export declare function DataTable(props: DataTableProps): JSX.Element;
