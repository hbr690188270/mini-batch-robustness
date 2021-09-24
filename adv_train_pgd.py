import numpy as np 
import torch
import torch.nn.functional as F
from PGDAttack import pgd_attack
from self_gcn import GCN
import pickle

from data_util import load_data, preprocess_features

import time

# C = 1. # initial  learning rate
# ATTACK = True
# Set random seed

adj, features, orig_y_train, orig_y_val, orig_y_test, all_orig_label, train_mask, val_mask, test_mask = load_data()

device = torch.device("cuda")

dense_feat = features.todense()
dense_adj = adj.todense()

num_nodes, num_features, num_classes = dense_feat.shape[0], dense_feat.shape[1], orig_y_train.shape[1]

train_mask = torch.tensor(train_mask).to(device)
valid_mask = torch.tensor(val_mask).to(device)
test_mask = torch.tensor(test_mask).to(device)

feature_mat = torch.FloatTensor(dense_feat).to(device)
feature_mat = feature_mat / feature_mat.sum(1, keepdim = True).clamp_(min=1.)
adj_mat = torch.FloatTensor(dense_adj).to(device)
num_edges = (torch.sum(adj_mat) / 2).item()
y_train = torch.tensor(orig_y_train).to(device)
# y_train = torch.argmax(y_train, dim = 1)

y_train = torch.argmax(y_train[train_mask], dim = 1)

y_valid = torch.tensor(orig_y_val).to(device)
y_valid = torch.argmax(y_valid[valid_mask], dim = 1)

y_test = torch.tensor(orig_y_test).to(device)
y_test = torch.argmax(y_test[test_mask], dim = 1)

y_all = torch.tensor(all_orig_label).to(device)
y_all = torch.argmax(y_all, dim = 1)


print("%d train, %d valid, %d test, total %d "%(len(y_train), len(y_valid), len(y_test), len(y_all)))


adj_mat = adj_mat + torch.eye(adj_mat.size(0)).to(device)
row_sum = torch.sum(adj_mat, dim = 1)
d_sqrt_inv = row_sum.pow_(-0.5).view(-1,1)
normalized_adj = torch.multiply(torch.multiply(d_sqrt_inv, adj_mat), d_sqrt_inv.view(1,-1))

model = GCN(num_features, 32, num_classes).to(device)

nat_support = normalized_adj.detach()
adv_support, new_adv_support = normalized_adj.detach(), normalized_adj.detach()


perturb_ratio = 0.05
lmd = 1
eps = num_edges * perturb_ratio
xi = 1e-5
mu = 200
train_steps = 1000
attack_steps = 20

loss_record = []
best_acc, best_loss = 0, 1e8

orig_adj = torch.tensor(dense_adj, dtype = torch.float, device = device, requires_grad=True)
attacker = pgd_attack(model, features = feature_mat, orig_adj = orig_adj, ratio = 0.05, device = device)

# train_label_mask = train_mask + test_mask
# train_label_mask = train_mask

# train_label = torch.argmax(torch.tensor(orig_y_train)[train_label_mask], dim = 1).to(device).long()
# attack_label_mask = train_mask+test_mask 
# attack_label = torch.argmax(torch.tensor(orig_y_train)[attack_label_mask], dim = 1).to(device).long()

optimizer = torch.optim.Adam([
    dict(params=model.conv1.parameters(), weight_decay=5e-4),
    dict(params=model.conv2.parameters(), weight_decay=0)
], lr=0.01) 

robust_acc = []
robust_loss = []
clean_acc = []
clean_loss = []

for n in range(train_steps):
    print('\n\n============================= iteration {}/{} =============================='.format(n+1, train_steps))
    print('TRAIN')

    model.train()
    old_adv_support = adv_support.detach()
    adv_support = new_adv_support.detach()

    print('support diff:',torch.sum(old_adv_support-adv_support))

    logits, prob = model(feature_mat, adv_support)
    loss = F.cross_entropy(logits[train_mask], y_train)
    acc = torch.argmax(prob[train_mask], dim = 1).eq(y_train).sum().item()/y_train.size(0)
    loss_record.append(loss.item())
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    print('[model outs] adv train acc: {}, adv train loss: {}'.format(acc, loss.item()))

    print('----------------------------------------------------------------------------')
    print('ATTACK')
    model.eval()
    new_adv_support = attacker.perturb(y_test, test_mask, k = attack_steps, visualize=False)

    if len(new_adv_support) == 0:
        print("fail to sample a valid modification...")
        new_adv_support = old_adv_support.detach()
        continue
    new_adv_support = torch.tensor(new_adv_support[0], device = device)
    logits, prob = model(feature_mat, new_adv_support)
    train_loss = F.cross_entropy(logits[train_mask], y_train)
    test_loss_adv = F.cross_entropy(logits[test_mask], y_test)

    train_acc = torch.argmax(prob[train_mask], dim = 1).eq(y_train).sum().item()/y_train.size(0)
    test_acc_adv = torch.argmax(prob[test_mask], dim = 1).eq(y_test).sum().item()/y_test.size(0)
    print('[adv support] train acc: {}, train loss: {}, test acc: {}, test loss: {}'.format(train_acc, train_loss, test_acc_adv, test_loss_adv))
    
    robust_acc.append(test_acc_adv)
    robust_loss.append(test_loss_adv)

    logits, prob = model(feature_mat, nat_support)
    train_loss = F.cross_entropy(logits[train_mask], y_train)
    test_loss_nat = F.cross_entropy(logits[test_mask], y_test)

    train_acc = torch.argmax(prob[train_mask], dim = 1).eq(y_train).sum().item()/y_train.size(0)
    test_acc_nat = torch.argmax(prob[test_mask], dim = 1).eq(y_test).sum().item()/y_test.size(0)

    clean_acc.append(test_acc_nat)
    clean_loss.append(test_loss_nat)

    print('[nat support] train acc: {}, train loss: {}, test acc: {}, test loss: {}'.format(train_acc, train_loss, test_acc_nat, test_loss_nat))
    
    if test_loss_adv < best_loss:
        best_loss = test_loss_adv
        torch.save(model, "./models/cora/rob_model.pt")

with open("./results/cora/result.pkl",'wb') as f:
    pickle.dump((robust_acc, robust_loss, clean_acc, clean_loss), f)


attacker.perturb(y_test = y_test, test_mask = test_mask, k = 200)




