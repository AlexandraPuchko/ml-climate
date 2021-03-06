import torch.nn as nn
import torch
import numpy as np
from torch.autograd import Variable
from cell import ConvLSTMCell

class ConvLSTM(nn.Module):

    def __init__(self,input_size, input_dim, hidden_dim, kernel_size, num_layers,
                 batch_first=True, bias=True, return_all_layers=False):
        super(ConvLSTM, self).__init__()

        self._check_kernel_size_consistency(kernel_size)

        # Make sure that both `kernel_size` and `hidden_dim` are lists having len == num_layers
        kernel_size = self._extend_for_multilayer(kernel_size, num_layers)
        hidden_dim  = self._extend_for_multilayer(hidden_dim, num_layers)

        if not len(kernel_size) == len(hidden_dim) == num_layers:
            raise ValueError('Inconsistent list length.')

        self.height, self.width = input_size
        self.input_dim  = input_dim
        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.num_layers = num_layers
        self.batch_first = batch_first
        self.bias = bias
        self.return_all_layers = return_all_layers
        self.decay_func = "linear" #might be changed to exp or negative sigmoid

        cell_list = []
        for i in range(0, self.num_layers):
            cur_input_dim = self.input_dim if i == 0 else self.hidden_dim[i-1]

            cell_list.append(ConvLSTMCell(input_size=(self.height, self.width),
                                          input_dim=cur_input_dim,
                                          hidden_dim=self.hidden_dim[i],
                                          kernel_size=self.kernel_size[i],
                                          bias=self.bias))
        #last conv layer
        padding_size = self.kernel_size[0][0] // 2, self.kernel_size[0][1] // 2
        cell_list.append(nn.Conv2d(in_channels=hidden_dim[-1],
                              out_channels=1,# precipitation value
                              kernel_size=(3,3),
                              padding=1,
                              bias=self.bias))
        #module list is like a Python list. It is similar to forward, but forward has its embedded forward method,
        # whereas we should redefine our own in ModuleList
        self.cell_list = nn.ModuleList(cell_list)
        self._hidden = self._init_hidden(1)


    #@profile
    def forward(self, input_x, hidden_state, epsilon, device, forward_mode,loss, step, dev_y):
        """

        Parameters
        ----------
        input_tensor: todo
            5-D Tensor either of shape (t, b, c, h, w) or (b, t, c, h, w)
        hidden_state: todo
            None. todo implement stateful

        Returns
        -------
        train_y_vals, last_layer_hidden_states
        """
        seq_len = 0
        if not self.batch_first:
            # (t, b, c, h, w) -> (b, t, c, h, w)
            input_x = input_tensor.permute(1, 0, 2, 3, 4)

        input_x = torch.from_numpy(input_x).float().to(device)

        #depending on the forward mode, we either output loss (if Validation) or train_outputs (if Train)
        mode_output = None

        if forward_mode == 'Train':
            if hidden_state is None:
                hidden_state = self._hidden
            else:
                hidden_state = [(h.detach(),c.detach()) for h,c in hidden_state]
            # #of months in a sequence
        seq_len = input_x.size(1)

        cur_layer_input = input_x
        train_x = cur_layer_input[:, 0, :, :, :]
        train_y = train_x

        #from t = 0 to T
        one_timestamp_output = []
        #take hidden states for first layer
        hidden_states = hidden_state
        # save all predicted maps to compute the loss
        train_y_vals = []

        forward_loss = []
        last_layer_hidden_states = None

        for t in range(seq_len):
            #NOTE: This is where we use scheduled sampling to set up our next input.
            #      Flip biased coin, take ground truth with a probability of 'epsilon'
            #      ELSE take model output.
            if forward_mode == 'Train':
                if np.random.binomial(1, epsilon, 1)[0]:
                    train_x = cur_layer_input[:, t, :, :, :]
                else:
                    train_x = train_y
            elif forward_mode == 'Validation':
                train_x = train_y


            for layer_idx in range(self.num_layers):
                h, c = self.cell_list[layer_idx](input_tensor=train_x,
                                                 cur_state=hidden_states[layer_idx])
                train_x = h
                one_timestamp_output.append([h, c])

            # save all pairs (c_i, h_i) to feed the next timestep
            hidden_states = one_timestamp_output
            #empty array of (h_i,c_i)
            one_timestamp_output = []
            #get predicted value of h from the last layer for t = i
            last_hidden_state = hidden_states[-1][0]
            train_y = self.cell_list[-1](last_hidden_state)


            if forward_mode == 'Train':
                train_y_vals.append(torch.squeeze(train_y, 0))
                #save last hidden states to fit the next sequence
                if t == seq_len - 1:
                    last_layer_hidden_states = hidden_states

            elif forward_mode == 'Validation':
                #compute loss for the image
                forward_loss.append(loss(torch.squeeze(train_y, 0), dev_y[t]).item())
                if t == 0:
                    #save hidden states from the first ground truth fitted value
                    # and use it as an initial state in the next sequence
                    last_layer_hidden_states = hidden_states


        if forward_mode == 'Train':
            #convert all outputs from the current sequence to tensor (stack along feature axes)
            train_y_vals = torch.stack(train_y_vals,dim=0)
            mode_output = train_y_vals
        elif forward_mode == 'Validation':
            mode_output = forward_loss

        return mode_output, last_layer_hidden_states




    def _init_hidden(self, batch_size):
        init_states = []
        for i in range(self.num_layers):
            init_states.append(self.cell_list[i].init_hidden(batch_size))
        return init_states

    @staticmethod
    def _check_kernel_size_consistency(kernel_size):
        if not (isinstance(kernel_size, tuple) or
                    (isinstance(kernel_size, list) and all([isinstance(elem, tuple) for elem in kernel_size]))):
            raise ValueError('`kernel_size` must be tuple or list of tuples')

    @staticmethod
    #apply same kernel for every layer, if we haven;t define different kernels per each layer
    def _extend_for_multilayer(param, num_layers):
        if not isinstance(param, list):
            param = [param] * num_layers
        return param
