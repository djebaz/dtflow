import * as React from "react";

export interface MetadataBlockProps {
  icon?: React.ReactNode;
  label: string;
  children?: React.ReactNode;
  minWidth?: number;
}
export declare function MetadataBlock(props: MetadataBlockProps): JSX.Element;
