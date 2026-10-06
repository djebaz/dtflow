import * as React from "react";

export interface ImageCardProps {
  src?: string;
  ratio?: string;
  /** Placeholder text when no image is supplied. */
  badge?: string;
  title?: string;
  subtitle?: string;
  onClick?: React.MouseEventHandler<HTMLDivElement>;
}
export declare function ImageCard(props: ImageCardProps): JSX.Element;
