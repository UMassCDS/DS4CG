from __future__ import print_function
from __future__ import division

import time
from abc import abstractmethod
import torch
import torchvision

from src.utils.util import log, load_config, generate_tag, save_path, save_roc, setup
from src.builders import model_builder, dataset_builder, optimizer_builder, criterion_builder

log.info("PyTorch Version: {}".format(torch.__version__))
log.info("Torchvision Version: {}".format(torchvision.__version__))


class BaseEngine(object):

    def __init__(self, config, tag):
        self.config = load_config(config)
        self.tag = generate_tag(tag)

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        if device == "cpu":
            log.warn("GPU is not available. Please check the configuration.")
        else:
            log.ward("GPU is available.")

    def train(self):
        self._train(self.config["train"])

    @abstractmethod
    def _train(self, train_config):
        pass

    @abstractmethod
    def _val(self):
        pass

    def eval(self):
        self._eval(self.config["eval"])

    @abstractmethod
    def _eval(self, eval_config):
        pass

    @abstractmethod
    def _save_model(self, save_dir, epoch):
        pass

class Engine(BaseEngine):

    def __init__(self, config, tag):
        super(Engine, self).__init__(config, tag)

        # build dataloader/model
        self.dataloader = dataset_builder.build(self.config['data'])
        self.model, misc = model_builder.build(self.config['model'])
        if torch.cuda.device_count() > 1:
            self.model = torch.nn.DataParallel(self.model)
            log.warn("{} GPUs will be used.".format(torch.cuda.device_count()))
        self.model.to(self.device)

        # misc information
        self.model_name = misc['model_name']
        self.num_classes = misc['num_classes']
        self.checkpoint = misc.get('checkpoint', None)
        self.is_inception = misc.get('is_inception', False)

        # build optimizer/criterion
        self.optimizer = optimizer_builder.build(
            self.config['train'], self.model.parameters(), self.checkpoint)
        self.criterion = criterion_builder.build(self.config['train'])

        setup(self.config['train'])

    def _train(self, train_config):
        save_dir = train_config.get('save_dir', 'checkpoints')

        start_epoch = 0 if self.checkpoint is None else self.checkpoint['epoch']
        num_epochs = train_config.get('num_epochs', 50)
        if num_epochs < start_epoch:
            num_epochs = start_epoch + 50

        log.info(
            "Training for {} epochs starts from epoch {}"\
            .format(num_epochs, start_epoch))

        val_accuracies = []
        best_acc = 0.0

        for epoch in range(start_epoch, num_epochs):
            train_start = time.time()
            train_loss = self._train_one_epoch()

            time_elapsed = time.time() - train_start
            log.info(
                'Epoch {} completed in {} - train loss: {:4f}'\
                .format(epoch, time_elapsed, train_loss)
            )

            val_start = time.time()
            val_loss, val_acc = self._val()

            # save the best model
            if val_acc > best_acc:
                best_acc = val_acc
                self._save_model(save_dir, epoch)

            val_accuracies.append(val_acc)
            time_elapsed = time.time() - val_start

            log.infov(
                'Epoch {} completed in {} - val loss: {:4f}, val accuracy {:4f}'\
                .format(epoch, time_elapsed, val_loss, val_acc))


    def _train_one_epoch(self):
        total_loss, num_corrects = 0.0, 0
        num_batches = len(self.dataloader['train'])
        self.model.train()

        for i, (inputs, labels) in enumerate(self.dataloader['train']):
            inputs = inputs.to(self.device)
            labels = labels.to(self.device).unsqueeze(-1).float()

            self.optimizer.zero_grad()

            # Forward propagation
            if self.is_inception:
                # Special case for inception because in training it has an auxiliary output
                # In training time, we calculate the loss by summing the final and auxiliary output
                # In inference, we only consider the final output
                outputs, aux_outputs = self.model(inputs)
                loss = self.criterion(outputs, labels) + \
                        0.4 * self.criterion(aux_outputs, labels)
            else:
                outputs = self.model(inputs)
                loss = self.criterion(outputs, labels)

            # Backward propagation
            loss.backward()
            self.optimizer.step()

             # Statistics
            total_loss += loss.item() * inputs.size(0)

            log.info(
                'Train batch {}/{} - loss: {:4f}'\
                .format(i, num_batches, loss))

        train_loss = total_loss / len(self.dataloader['train'].dataset)
        return train_loss


    def _val(self):
        total_loss, num_corrects = 0.0, 0
        self.model.eval()

        for inputs, labels in self.dataloader['val']:
            inputs = inputs.to(self.device)
            labels = labels.to(self.device).unsqueeze(-1).float()

            # Forward propagation
            outputs = self.model(inputs)
            loss = self.criterion(outputs, labels)

            # Use softmax when the num of classes > 1 else sigmoid
            if self.num_clsses > 1:
                _, predictions = torch.max(outputs, 1)
            else:
                probabilities = torch.sigmoid(outputs)
                predictions = torch.gt(probabilities, 0.5)

            # Statistics
            total_loss += loss.item() * inputs.size(0)
            num_corrects += torch.sum(predictions == labels.data)

        val_loss = total_loss / len(self.dataloader['val'].dataset)
        val_acc = num_corrects.double() / len(self.dataloader['val'].dataset)
        return val_loss, val_acc


    def _eval(self, eval_config):
        use_roc = eval_config.get('use_roc', False)
        total_loss, num_corrects = 0.0, 0
        self.model.eval()

        inputs, labels = self.dataloader['eval'].next()
        inputs = inputs.to(self.device)
        labels = labels.to(self.device).unsqueeze(-1).float()

        # Forward propagation
        outputs = self.model(inputs)
        loss = self.criterion(outputs, labels)

        # Use softmax when the num of classes > 1 else sigmoid
        if self.num_clsses > 1:
            _, predictions = torch.max(outputs, 1)
        else:
            probabilities = torch.sigmoid(outputs)
            predictions = torch.gt(probabilities, 0.5)
            if use_roc:
                # TODO: implement save_roc
                save_roc(probabilities, labels)

        # Statistics
        total_loss += loss.item() * inputs.size(0)
        num_corrects += torch.sum(predictions == labels.data)

        eval_loss = total_loss / len(self.dataloader['eval'].dataset)
        eval_acc = num_corrects.double() / len(self.dataloader['eval'].dataset)
        return eval_loss, eval_acc


    def _save_model(self, save_dir, epoch):
        checkpoint_path = save_path(save_dir, self.model_name, self.tag)

        torch.save({
            'epoch': epoch,
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict()
        }, checkpoint_path)

