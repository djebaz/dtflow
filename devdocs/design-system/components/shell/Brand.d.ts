import * as React from "react";

export interface BrandProps {
  title?: string;
  subtitle?: string;
  /** Logo URL relative to the page. */
  logo?: string;
  /** Slot after the title, e.g. the Inspector toggle. */
  children?: React.ReactNode;
}
export declare function Brand(props: BrandProps): JSX.Element;
