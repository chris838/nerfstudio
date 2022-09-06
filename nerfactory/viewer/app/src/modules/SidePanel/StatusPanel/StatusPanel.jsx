import * as React from 'react';

import Button from '@mui/material/Button';
import { ButtonGroup } from '@mui/material';
import { useSelector } from 'react-redux';
import ViewInArIcon from '@mui/icons-material/ViewInAr';
import WebSocketUrlField from '../../WebSocketUrlField';

interface StatusPanelProps {
  sceneTree: object;
}

export default function StatusPanel(props: StatusPanelProps) {
  const sceneTree = props.sceneTree;

  const isWebsocketConnected = useSelector(
    (state) => state.websocketState.isConnected,
  );
  const isWebrtcConnected = useSelector(
    (state) => state.webrtcState.isConnected,
  );
  const eval_fps = useSelector((state) => state.renderingState.eval_fps);
  const train_eta = useSelector((state) => state.renderingState.train_eta);
  const vis_train_ratio = useSelector(
    (state) => state.renderingState.vis_train_ratio,
  );

  // simple toggle button
  const [value, setValue] = React.useState(1);
  const handleChange = () => {
    setValue(!value);
  };
  const scene_button = value ? 'Hide Scene' : 'Show Scene';
  sceneTree.object.visible = value;

  return (
    <div className="StatusPanel">
      <ButtonGroup
        className="StatusPanel-button-group"
        variant="contained"
        aria-label="outlined button group"
      >
        <Button
          className="StatusPanel-button"
          variant="contained"
          disabled={!isWebsocketConnected}
          style={{ textTransform: 'none' }}
        >
          Websocket Connected
        </Button>
        <Button
          className="StatusPanel-button"
          variant="contained"
          disabled={!isWebrtcConnected}
          style={{ textTransform: 'none' }}
        >
          WebRTC Connected
        </Button>
      </ButtonGroup>
      <Button // button with view in ar icon
        className="StatusPanel-hide-scene-button"
        variant="outlined"
        onClick={handleChange}
        style={{ textTransform: 'none' }}
        startIcon={<ViewInArIcon />}
      >
        {scene_button}
      </Button>
      <WebSocketUrlField />
      <div className="StatusPanel-metrics">
        <div>
          <b>Eval FPS:</b> {eval_fps}
        </div>
        <div>
          <b>Train ETA:</b> {train_eta}
        </div>
        <div>
          <b>Time Allocation:</b> {vis_train_ratio}
        </div>
      </div>
    </div>
  );
}
