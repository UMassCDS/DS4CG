import argparse
import torch

from src.engines import Engine

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default='resnet18',
                        help="Config name")
    parser.add_argument('--tag', default='',
                        help="tag to discern evaluation instances")
    args = parser.parse_args()

    engine = Engine(mode='eval', config=args.config, tag=args.tag)
    engine.eval()
