import type { CharacterRigState } from "../schema/directorProject";
import { PrimitiveMannequin } from "./PrimitiveMannequin";
import type { CharacterBodyType } from "./mannequin/bodyTypes";

interface CharacterModelProps {
  bodyType?: CharacterBodyType;
  color?: string;
  onLabelAnchorYChange?: (anchorY: number) => void;
  rigState?: CharacterRigState;
}

export function CharacterModel({ bodyType, color, onLabelAnchorYChange, rigState }: CharacterModelProps) {
  // The upstream demo defaults to a separately licensed Sketchfab mesh. The
  // embedded product uses StoryAI's procedural mannequin so the distributed
  // editor remains self-contained and the pose/body controls still work.
  void onLabelAnchorYChange;
  return <PrimitiveMannequin bodyType={bodyType} color={color} rigState={rigState} />;
}
