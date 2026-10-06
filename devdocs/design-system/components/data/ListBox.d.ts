import * as React from "react";

export interface ListBoxProps {
  children?: React.ReactNode;
  minHeight?: number;
  label?: string;
  style?: React.CSSProperties;
}
export declare function ListBox(props: ListBoxProps): JSX.Element;
